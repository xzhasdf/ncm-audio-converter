from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ncm_audio_converter.ncm_decoder import NcmDecodeError, decode_ncm_to_temp

SUPPORTED_FORMATS = {"mp3", "wav", "flac", "m4a", "ogg"}
DEFAULT_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
    ".ncm",
}


def parse_extensions(raw: str) -> set[str]:
    return {
        ext.strip().lower() if ext.strip().startswith(".") else f".{ext.strip().lower()}"
        for ext in raw.split(",")
        if ext.strip()
    }


@dataclass(slots=True)
class ConvertTask:
    src: Path
    dst: Path


@dataclass(slots=True)
class ConvertResult:
    task: ConvertTask
    success: bool
    message: str


def ensure_ffmpeg() -> None:
    _get_ffmpeg_bin()


def _get_ffmpeg_bin() -> str:
    env_path = os.environ.get("NCM_FFMPEG_PATH", "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        candidates.append(str(base / "ffmpeg.exe"))
        candidates.append(str(base / "ffmpeg"))

    which_path = shutil.which("ffmpeg")
    if which_path:
        candidates.append(which_path)

    if sys.platform == "darwin":
        candidates.extend(
            [
                "/opt/homebrew/bin/ffmpeg",
                "/usr/local/bin/ffmpeg",
                "/opt/local/bin/ffmpeg",
            ]
        )
    elif sys.platform.startswith("win"):
        candidates.extend(
            [
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            ]
        )
    else:
        candidates.extend(["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"])

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))

    raise RuntimeError(
        "未找到 ffmpeg，请先安装并确保命令可用。可通过环境变量 NCM_FFMPEG_PATH 指定 ffmpeg 完整路径。"
    )


def find_audio_files(input_dir: Path, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in input_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in extensions:
            files.append(path)
    return files


def build_tasks(
    files: Iterable[Path],
    input_dir: Path,
    output_dir: Path,
    target_format: str,
) -> list[ConvertTask]:
    tasks: list[ConvertTask] = []
    for src in files:
        relative = src.relative_to(input_dir)
        dst = output_dir / relative
        dst = dst.with_suffix(f".{target_format}")
        tasks.append(ConvertTask(src=src, dst=dst))
    return tasks


def _build_ffmpeg_command(
    ffmpeg_bin: str,
    input_path: Path,
    output_path: Path,
    target_format: str,
    bitrate: str,
) -> list[str]:
    bitrate_value = bitrate.strip().lower()
    lossless = bitrate_value in {"lossless", "无损"}

    common = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-map_metadata",
        "0",
    ]

    if target_format == "mp3":
        if lossless:
            raise ValueError("mp3 不支持真正无损，请选择 flac/wav 或 m4a(无损)")
        return common + ["-codec:a", "libmp3lame", "-b:a", bitrate, str(output_path)]
    if target_format == "m4a":
        if lossless:
            return common + ["-codec:a", "alac", str(output_path)]
        return common + ["-codec:a", "aac", "-b:a", bitrate, str(output_path)]
    if target_format == "ogg":
        if lossless:
            raise ValueError("ogg(vorbis) 不支持真正无损，请选择 flac/wav 或 m4a(无损)")
        return common + ["-codec:a", "libvorbis", "-b:a", bitrate, str(output_path)]
    if target_format == "flac":
        return common + ["-codec:a", "flac", str(output_path)]
    if target_format == "wav":
        return common + ["-codec:a", "pcm_s16le", str(output_path)]
    if target_format == "wma":
        return common + ["-codec:a", "wmalossless", str(output_path)]
    raise ValueError(f"不支持的格式: {target_format}")


def _run_ffmpeg(
    ffmpeg_bin: str,
    input_path: Path,
    output_path: Path,
    target_format: str,
    bitrate: str,
    overwrite: bool,
) -> tuple[bool, str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        return True, "skip: 目标文件已存在"
    cmd = _build_ffmpeg_command(ffmpeg_bin, input_path, output_path, target_format, bitrate)
    if not overwrite:
        cmd[1] = "-n"
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if proc.stderr and proc.stderr.strip():
            return True, f"ok (warn: {proc.stderr.strip()})"
        return True, "ok"
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            return False, f"ffmpeg failed(exit={exc.returncode}): {stderr}"
        return False, f"ffmpeg failed(exit={exc.returncode})"


def convert_one(
    task: ConvertTask,
    target_format: str,
    bitrate: str,
    overwrite: bool,
    flac_fallback: bool = False,
) -> ConvertResult:
    task.dst.parent.mkdir(parents=True, exist_ok=True)

    if task.dst.exists() and not overwrite:
        return ConvertResult(task=task, success=True, message="skip: 目标文件已存在")

    input_path = task.src
    temp_decoded: Path | None = None
    prefix_note = ""
    if task.src.suffix.lower() == ".ncm":
        try:
            decoded = decode_ncm_to_temp(task.src)
        except NcmDecodeError as exc:
            return ConvertResult(task=task, success=False, message=f".ncm 解码失败: {exc}")
        input_path = decoded.path
        temp_decoded = decoded.path
        payload_fmt = decoded.path.suffix.lstrip(".") or "unknown"
        prefix_note = f"ncm内音频={payload_fmt} -> 目标={target_format}; "

    final_task = task

    try:
        ffmpeg_bin = _get_ffmpeg_bin()
        ok, message = _run_ffmpeg(ffmpeg_bin, input_path, task.dst, target_format, bitrate, overwrite)
        if ok:
            return ConvertResult(task=final_task, success=True, message=f"{prefix_note}{message}")

        if flac_fallback and target_format == "flac":
            fallback_chain = [
                ("wav", task.dst.with_suffix(".wav"), "flac失败->降级wav"),
                ("wma", task.dst.with_suffix(".wma"), "wav失败->降级wma(无损)"),
                ("mp3", task.dst.with_suffix(".mp3"), "wma失败->降级mp3"),
            ]
            failure_messages = [f"flac失败: {message}"]
            for fmt, dst, note in fallback_chain:
                ok_fb, msg_fb = _run_ffmpeg(ffmpeg_bin, input_path, dst, fmt, bitrate, overwrite)
                if ok_fb:
                    final_task = ConvertTask(src=task.src, dst=dst)
                    return ConvertResult(task=final_task, success=True, message=f"{prefix_note}{note}; {msg_fb}")
                failure_messages.append(f"{fmt}失败: {msg_fb}")
            return ConvertResult(task=task, success=False, message=f"{prefix_note}" + " | ".join(failure_messages))

        hint = ""
        if task.src.suffix.lower() == ".ncm":
            hint = "；提示: .ncm 可能需要先解密/转换为可解码音频后再转码"
        return ConvertResult(task=task, success=False, message=f"{prefix_note}{message}{hint}")
    except ValueError as exc:
        return ConvertResult(task=task, success=False, message=f"{prefix_note}{exc}")
    finally:
        if temp_decoded is not None:
            temp_decoded.unlink(missing_ok=True)


def convert_batch(
    tasks: list[ConvertTask],
    target_format: str,
    bitrate: str,
    overwrite: bool,
    workers: int,
    flac_fallback: bool = False,
) -> list[ConvertResult]:
    results: list[ConvertResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(convert_one, task, target_format, bitrate, overwrite, flac_fallback)
            for task in tasks
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results
