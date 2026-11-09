"""写入安全：备份、原子写、失败不覆盖、路径边界。

只有用户明确确认写入英文 TXT 时才会写源目录；检查/翻译/预览均只读。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.common.fingerprint import hash_file
from modules.common.path_guard import (
    is_relative_to,
    normalize_path,
    to_long_path,
)

from .errors import PrecheckError

# 读取顺序：先严格 UTF-8，再 GB18030（兼容 GBK），最后 UTF-16
TEXT_ENCODINGS = ("utf-8", "gb18030", "utf-16")


def decode_bytes(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise PrecheckError("PC-006", "TXT 编码无法识别（支持 UTF-8 / GBK / UTF-16）")


def _decode(raw: bytes) -> tuple[str, str]:
    return decode_bytes(raw)


def read_text_file(path: str, *, max_bytes: int = 1_000_000) -> tuple[str, str]:
    """只读读取 TXT；返回 (文本, 编码)。"""
    target = normalize_path(path)
    try:
        size = os.path.getsize(target)
    except OSError as exc:
        raise PrecheckError("PC-006", f"无法读取文件：{exc}") from exc
    if size > max_bytes:
        raise PrecheckError(
            "PC-007", f"文件 {os.path.basename(target)} 为 {size} 字节，超过上限"
        )
    try:
        with open(target, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise PrecheckError("PC-006", f"无法读取文件：{exc}") from exc
    if len(raw) > max_bytes:
        raise PrecheckError("PC-007", "文件超过读取上限")
    text, encoding = _decode(raw)
    return text, encoding


def text_length(text: str) -> int:
    return len("".join((text or "").split()))


def word_count(text: str) -> int:
    return len((text or "").split())


def ensure_within_root(path: str, root: str) -> str:
    """规范化并确认 path 位于 root 内（含 root 本身）。"""
    target = normalize_path(path)
    base = normalize_path(root)
    if os.path.normcase(target) == os.path.normcase(base):
        return target
    if not is_relative_to(target, base):
        raise PrecheckError("PC-202", f"写入路径不在根目录内：{target}")
    return target


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def normalize_output(text: str, *, newline: str = "\n") -> str:
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        raise PrecheckError("PC-109", "英文内容为空，未写入")
    return body.replace("\n", newline)


def atomic_write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> str:
    """同目录临时文件 + fsync + os.replace；失败时清理临时文件。"""
    target = normalize_path(path)
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        raise PrecheckError("PC-204", f"目标目录不存在：{parent}")
    temp = os.path.join(
        parent, f".{os.path.basename(target)}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        with open(temp, "w", encoding=encoding, newline=newline) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except OSError as exc:
        raise PrecheckError("PC-204", f"原子写入失败：{exc}") from exc
    finally:
        if os.path.exists(temp):
            try:
                os.unlink(temp)
            except OSError:
                pass
    return target


def backup_file(path: str, backup_root: str, job_id: str) -> str:
    """写入前备份到管理数据目录，并校验备份哈希。"""
    source = normalize_path(path)
    if not os.path.isfile(source):
        return ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(backup_root) / "precheck" / (job_id or "manual") / stamp
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / os.path.basename(source)
    index = 2
    while candidate.exists():
        candidate = folder / f"{Path(source).stem}_{index}{Path(source).suffix}"
        index += 1
    try:
        raw = Path(source).read_bytes()
        with open(candidate, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PrecheckError("PC-203", f"备份失败：{exc}") from exc
    if hash_file(candidate) != hash_file(source):
        raise PrecheckError("PC-203", "备份校验失败，已中止写入")
    return str(candidate)


def read_existing_text(path: str) -> tuple[str, str]:
    if not os.path.isfile(path):
        return "", ""
    return read_text_file(path)


def write_translation(
    zh_path: str,
    en_path: str,
    english: str,
    *,
    source_root: str,
    backup_root: str,
    job_id: str,
    overwrite: bool = False,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """写入英文 TXT。

    - 目标已存在且非空时，未显式 overwrite 直接拒绝；
    - 覆盖前先备份到管理数据目录；
    - 使用原子写；写后读回校验；
    - 换行风格尽量与中文 TXT 保持一致。
    """
    target = ensure_within_root(en_path, source_root)
    ensure_within_root(zh_path, source_root)
    if not target:
        raise PrecheckError("PC-202", "英文 TXT 路径为空")
    if zh_path and os.path.normcase(normalize_path(zh_path)) == os.path.normcase(target):
        raise PrecheckError("PC-202", "英文 TXT 与中文 TXT 是同一个文件，已中止写入")

    existed = os.path.isfile(target)
    existing_text = ""
    if existed:
        existing_text, _ = read_existing_text(target)
        if existing_text.strip() and not overwrite:
            raise PrecheckError(
                "PC-201",
                f"英文 TXT 已存在且非空：{os.path.basename(target)}；"
                "如确需覆盖，请显式勾选“覆盖非空英文”",
            )

    newline = "\n"
    zh_text = ""
    if os.path.isfile(zh_path):
        zh_text, _ = read_text_file(zh_path)
        newline = _newline_of(zh_text)

    body = normalize_output(english, newline=newline)
    if zh_text.endswith(("\n", "\r")):
        body += newline

    backup_path = backup_file(target, backup_root, job_id) if existed else ""
    written = atomic_write_text(target, body, encoding=encoding, newline="")

    check_text, _ = read_text_file(written)
    if check_text != body:
        raise PrecheckError("PC-204", "写入校验失败：读回内容与预期不一致")

    return {
        "path": written,
        "backup_path": backup_path,
        "replaced": existed and bool(existing_text.strip()),
        "bytes": len(body.encode(encoding)),
        "encoding": encoding,
    }


def describe_backup_dir(backup_root: str, job_id: str) -> str:
    return str(Path(normalize_path(backup_root)) / "precheck" / (job_id or "manual"))
