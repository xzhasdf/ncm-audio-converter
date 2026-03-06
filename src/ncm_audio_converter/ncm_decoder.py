from __future__ import annotations

import base64
import json
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CORE_KEY_HEX = "687A4852416D736F356B496E62617857"
META_KEY_HEX = "2331346C6A6B5F215C5D2630553C2728"


class NcmDecodeError(RuntimeError):
    pass


@dataclass(slots=True)
class DecodeResult:
    path: Path
    note: str


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise NcmDecodeError("AES 解密结果为空")
    pad = data[-1]
    if pad <= 0 or pad > 16:
        raise NcmDecodeError("AES 填充无效")
    if data[-pad:] != bytes([pad]) * pad:
        raise NcmDecodeError("AES 填充校验失败")
    return data[:-pad]


def _aes_ecb_decrypt(ciphertext: bytes, key_hex: str) -> bytes:
    if len(ciphertext) % 16 != 0:
        raise NcmDecodeError("NCM 数据块长度异常")
    try:
        key = bytes.fromhex(key_hex)
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    except Exception as exc:
        raise NcmDecodeError(f"AES 解密失败: {exc}") from exc
    return _pkcs7_unpad(plaintext)


def _read_le_u32(raw: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(raw):
        raise NcmDecodeError("NCM 数据结构损坏")
    return struct.unpack("<I", raw[offset : offset + 4])[0], offset + 4


def _build_key_box(key_data: bytes) -> list[int]:
    box = list(range(256))
    c = 0
    last_byte = 0
    key_offset = 0
    key_length = len(key_data)
    for i in range(256):
        swap = box[i]
        c = (swap + last_byte + key_data[key_offset]) & 0xFF
        key_offset += 1
        if key_offset >= key_length:
            key_offset = 0
        box[i] = box[c]
        box[c] = swap
        last_byte = c
    return box


def decode_ncm_to_temp(src: Path) -> DecodeResult:
    raw = src.read_bytes()
    if len(raw) < 16:
        raise NcmDecodeError("NCM 文件过小或已损坏")
    if raw[:8] != b"CTENFDAM":
        raise NcmDecodeError("不是有效的 .ncm 文件")

    offset = 10
    key_length, offset = _read_le_u32(raw, offset)
    key_block = bytearray(raw[offset : offset + key_length])
    if len(key_block) != key_length:
        raise NcmDecodeError("NCM key 数据不完整")
    offset += key_length
    for i in range(len(key_block)):
        key_block[i] ^= 0x64

    key_data = _aes_ecb_decrypt(bytes(key_block), CORE_KEY_HEX)
    if len(key_data) <= 17:
        raise NcmDecodeError("NCM key 解密结果异常")
    key_data = key_data[17:]
    key_box = _build_key_box(key_data)

    meta_length, offset = _read_le_u32(raw, offset)
    meta_block = bytearray(raw[offset : offset + meta_length])
    offset += meta_length
    format_hint = "audio"
    if meta_block:
        for i in range(len(meta_block)):
            meta_block[i] ^= 0x63
        try:
            meta_payload = base64.b64decode(bytes(meta_block)[22:])
            meta_plain = _aes_ecb_decrypt(meta_payload, META_KEY_HEX)
            meta_json = json.loads(meta_plain[6:].decode("utf-8", "ignore"))
            fmt = str(meta_json.get("format", "")).strip().lower()
            if fmt:
                format_hint = fmt
        except Exception:
            pass

    _, offset = _read_le_u32(raw, offset)  # crc32
    offset += 5
    image_size, offset = _read_le_u32(raw, offset)
    offset += image_size
    if offset >= len(raw):
        raise NcmDecodeError("NCM 音频数据为空")
    encrypted_audio = raw[offset:]

    out_fd, out_path_str = tempfile.mkstemp(prefix="ncm_decoded_", suffix=f".{format_hint}")
    out_path = Path(out_path_str)
    try:
        with open(out_fd, "wb", closefd=True) as out:
            chunk_size = 0x8000
            for chunk_start in range(0, len(encrypted_audio), chunk_size):
                chunk = bytearray(encrypted_audio[chunk_start : chunk_start + chunk_size])
                for i in range(len(chunk)):
                    j = (i + 1) & 0xFF
                    chunk[i] ^= key_box[(key_box[j] + key_box[(key_box[j] + j) & 0xFF]) & 0xFF]
                out.write(chunk)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise

    return DecodeResult(path=out_path, note=f"ncm decoded to {out_path.suffix}")
