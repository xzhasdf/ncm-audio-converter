from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ncm_audio_converter.converter import (
    DEFAULT_EXTENSIONS,
    SUPPORTED_FORMATS,
    build_tasks,
    convert_batch,
    ensure_ffmpeg,
    find_audio_files,
    parse_extensions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量音频格式转换工具")
    parser.add_argument("--input", required=True, help="输入目录")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument(
        "--format",
        required=True,
        choices=sorted(SUPPORTED_FORMATS),
        help="目标音频格式",
    )
    parser.add_argument("--bitrate", default="320k", help="有损编码比特率，如 192k/320k")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--overwrite", action="store_true", help="覆盖输出目录中已有文件")
    parser.add_argument(
        "--flac-fallback",
        action="store_true",
        help="当目标格式为 flac 且失败时，按 WAV > WMA(无损) > MP3 自动降级",
    )
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="要扫描的扩展名，逗号分隔，例如 .mp3,.flac,.ncm",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"输入目录不存在或不可用: {input_dir}", file=sys.stderr)
        return 2

    if args.workers < 1:
        print("workers 必须 >= 1", file=sys.stderr)
        return 2

    extensions = parse_extensions(args.extensions)

    try:
        ensure_ffmpeg()
    except RuntimeError as err:
        print(str(err), file=sys.stderr)
        return 2

    files = find_audio_files(input_dir, extensions)
    if not files:
        print("未找到可转换音频文件。")
        return 0

    tasks = build_tasks(files=files, input_dir=input_dir, output_dir=output_dir, target_format=args.format)

    print(f"发现 {len(tasks)} 个文件，开始转码到 {args.format} ...")

    results = convert_batch(
        tasks=tasks,
        target_format=args.format,
        bitrate=args.bitrate,
        overwrite=args.overwrite,
        workers=args.workers,
        flac_fallback=args.flac_fallback,
    )

    success = sum(1 for r in results if r.success)
    failed = len(results) - success

    for r in sorted(results, key=lambda item: str(item.task.src)):
        status = "OK" if r.success else "FAIL"
        print(f"[{status}] {r.task.src} -> {r.task.dst} ({r.message})")

    print("-" * 60)
    print(f"总数: {len(results)}  成功: {success}  失败: {failed}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
