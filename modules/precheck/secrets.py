"""API Key 安全存储：Windows DPAPI + 掩码。

- 只用 DPAPI（当前用户）加密，不写入明文；
- 密钥文件位于管理数据目录 `secrets/precheck_keys.json`；
- 任何日志、报告、异常信息都不得包含密钥。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

from modules.common.timeutil import now_iso
from modules.config import app_config

_ENTROPY = b"VelkozPro.precheck.v1"
_DESCRIPTION = "VelkozPro precheck api key"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_FILE_NAME = "precheck_keys.json"


class SecretsError(Exception):
    """密钥存储错误；message 不包含密钥内容。"""


class SecretsUnavailable(SecretsError):
    """当前环境不支持 DPAPI。"""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _make_blob(data: bytes) -> tuple[_DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return blob, buffer


def _configure() -> None:
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise SecretsUnavailable("DPAPI 仅在 Windows 上可用；未保存任何密钥")
    _configure()
    blob_in, _buf_in = _make_blob(data)
    blob_entropy, _buf_entropy = _make_blob(_ENTROPY)
    blob_out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            _DESCRIPTION,
            ctypes.byref(blob_entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
    else:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            ctypes.byref(description),
            ctypes.byref(blob_entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
    if not ok:
        raise SecretsError("DPAPI 操作失败（密钥可能来自其他 Windows 用户）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect_text(plain: str) -> str:
    return base64.b64encode(_dpapi(plain.encode("utf-8"), protect=True)).decode("ascii")


def unprotect_text(blob: str) -> str:
    raw = base64.b64decode(blob.encode("ascii"))
    return _dpapi(raw, protect=False).decode("utf-8")


def mask_key(key: str) -> str:
    """界面/日志掩码。密钥永不明文显示。"""
    text = (key or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}{'*' * max(4, len(text) - 8)}{text[-4:]}"


def redact(text: str, secret: str) -> str:
    if not text or not secret:
        return text
    return text.replace(secret, "***")


class SecretsStore:
    """当前用户的密钥仓库。文件只保存 DPAPI 密文。"""

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else app_config.get_data_dir()
        self.path = self.base_dir / "secrets" / _FILE_NAME

    def available(self) -> bool:
        try:
            protect_text("ping")
            return True
        except SecretsError:
            return False

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "keys": {}}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise SecretsError(f"密钥文件无法读取：{type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise SecretsError("密钥文件格式不正确")
        data.setdefault("version", 1)
        keys = data.get("keys")
        if not isinstance(keys, dict):
            keys = {}
        data["keys"] = keys
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp"
        )
        text = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def set_key(self, profile_id: str, key: str) -> None:
        if not profile_id:
            raise SecretsError("缺少配置 ID")
        plain = (key or "").strip()
        if not plain:
            raise SecretsError("API Key 不能为空")
        data = self._load()
        data["keys"][profile_id] = {
            "blob": protect_text(plain),
            "updated_at": now_iso(),
            "preview": mask_key(plain),
        }
        self._save(data)

    def get_key(self, profile_id: str) -> str:
        entry = self._load()["keys"].get(profile_id)
        if not entry:
            return ""
        try:
            return unprotect_text(str(entry.get("blob") or ""))
        except (SecretsError, ValueError) as exc:
            raise SecretsError("API Key 解密失败；请重新保存") from exc

    def has_key(self, profile_id: str) -> bool:
        return bool(profile_id) and profile_id in self._load()["keys"]

    def preview(self, profile_id: str) -> str:
        entry = self._load()["keys"].get(profile_id) or {}
        return str(entry.get("preview") or "已保存")

    def delete_key(self, profile_id: str) -> None:
        data = self._load()
        if profile_id in data["keys"]:
            data["keys"].pop(profile_id, None)
            self._save(data)

    def profile_ids(self) -> list[str]:
        return sorted(self._load()["keys"].keys())

    def __repr__(self) -> str:  # 防止误打印密文
        return f"SecretsStore(path={self.path!s}, keys={len(self.profile_ids())})"


def environment_note() -> str:
    if sys.platform != "win32":
        return "当前系统不是 Windows，无法使用 DPAPI 保存 API Key。"
    return "API Key 使用 Windows DPAPI 加密，仅当前 Windows 用户可解密。"
