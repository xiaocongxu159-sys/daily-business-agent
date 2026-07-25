# -*- coding: utf-8 -*-
"""Windows-local encrypted storage for Lingxing credentials and sync state.

The encrypted credential file is protected with Windows DPAPI and can only be
decrypted by the same Windows user account. Non-secret sync state and the
sanitized shop list are stored beside it so the UI can show the last successful
result immediately while a background refresh is running.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class SecretProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...
    def unprotect(self, value: bytes) -> bytes: ...


@dataclass(frozen=True)
class LingxingCredentials:
    app_id: str
    app_secret: str
    proxy_url: str
    auto_sync: bool = True
    sync_interval_minutes: int = 120

    def validated(self) -> "LingxingCredentials":
        if not self.app_id.strip() or not self.app_secret.strip():
            raise ValueError("AppID 和 AppSecret 不能为空")
        if not self.proxy_url.strip():
            raise ValueError("固定出口代理地址不能为空")
        if not 30 <= int(self.sync_interval_minutes) <= 24 * 60:
            raise ValueError("自动同步间隔必须在 30 分钟到 24 小时之间")
        return self


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _to_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    if not value:
        buffer = ctypes.create_string_buffer(b"\0")
        return _DataBlob(0, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer
    buffer = ctypes.create_string_buffer(value, len(value))
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class WindowsDpapiProtector:
    """Protect secrets for the current Windows user with DPAPI."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01
    _entropy = b"DailyBusinessAgent.Lingxing.v1"

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI is only available on Windows")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, value: bytes) -> bytes:
        input_blob, input_buffer = _to_blob(value)
        entropy_blob, entropy_buffer = _to_blob(self._entropy)
        output_blob = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "DailyBusinessAgent Lingxing",
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        _ = input_buffer, entropy_buffer
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, value: bytes) -> bytes:
        input_blob, input_buffer = _to_blob(value)
        entropy_blob, entropy_buffer = _to_blob(self._entropy)
        output_blob = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        _ = input_buffer, entropy_buffer
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(output_blob.pbData)


class TestOnlyProtector:
    """Deterministic protector used only by automated tests."""

    _prefix = b"DAILY-AGENT-TEST:"
    _mask = b"\x8b\x31\xd0\x54\x9a\x27\x61\xc3"

    def protect(self, value: bytes) -> bytes:
        raw = self._prefix + value
        return bytes(byte ^ self._mask[index % len(self._mask)] for index, byte in enumerate(raw))

    def unprotect(self, value: bytes) -> bytes:
        raw = bytes(byte ^ self._mask[index % len(self._mask)] for index, byte in enumerate(value))
        if not raw.startswith(self._prefix):
            raise ValueError("encrypted credential file is invalid")
        return raw[len(self._prefix):]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class LingxingLocalStore:
    """Own all Lingxing files under the local Agent data directory."""

    def __init__(self, data_root: Path, protector: SecretProtector | None = None):
        self.root = Path(data_root).resolve() / "lingxing"
        self.credentials_path = self.root / "credentials.dpapi"
        self.state_path = self.root / "sync_state.json"
        self.shops_path = self.root / "shops.json"
        self._protector = protector

    def _get_protector(self) -> SecretProtector:
        if self._protector is not None:
            return self._protector
        return WindowsDpapiProtector()

    def has_credentials(self) -> bool:
        return self.credentials_path.is_file()

    def save_credentials(self, credentials: LingxingCredentials) -> None:
        payload = json.dumps(
            asdict(credentials.validated()),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        protected = self._get_protector().protect(payload)
        envelope = {
            "version": 1,
            "algorithm": "windows-dpapi-current-user",
            "ciphertext": base64.b64encode(protected).decode("ascii"),
        }
        encoded = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        if credentials.app_secret.encode("utf-8") in encoded:
            raise RuntimeError("credential encryption failed closed")
        _atomic_write(self.credentials_path, encoded)
        state = self.load_state()
        state.update({
            "configured": True,
            "app_id_hint": self._mask_app_id(credentials.app_id),
            "auto_sync": credentials.auto_sync,
            "sync_interval_minutes": credentials.sync_interval_minutes,
            "updated_at": _utc_now(),
        })
        self.save_state(state)

    def load_credentials(self) -> LingxingCredentials:
        envelope = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        protected = base64.b64decode(envelope["ciphertext"], validate=True)
        payload = self._get_protector().unprotect(protected)
        values = json.loads(payload.decode("utf-8"))
        return LingxingCredentials(**values).validated()

    def disconnect(self) -> None:
        self.credentials_path.unlink(missing_ok=True)
        state = self.load_state()
        state.update({
            "configured": False,
            "status": "disconnected",
            "message": "领星连接已断开，本地历史店铺数据仍保留。",
            "updated_at": _utc_now(),
        })
        state.pop("app_id_hint", None)
        self.save_state(state)

    @staticmethod
    def _mask_app_id(value: str) -> str:
        value = value.strip()
        if len(value) <= 4:
            return "*" * len(value)
        return "*" * max(4, len(value) - 4) + value[-4:]

    def load_state(self) -> dict:
        if not self.state_path.is_file():
            return {
                "configured": self.has_credentials(),
                "status": "not_configured",
                "message": "尚未配置领星连接。",
                "shops_count": 0,
            }
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {
                "configured": self.has_credentials(),
                "status": "state_error",
                "message": "本地同步状态文件无法读取。",
                "shops_count": len(self.load_shops()),
            }
        value["configured"] = self.has_credentials()
        value["shops_count"] = len(self.load_shops())
        return value

    def save_state(self, state: dict) -> None:
        safe = dict(state)
        for forbidden in ("app_id", "app_secret", "proxy_url", "access_token", "refresh_token"):
            safe.pop(forbidden, None)
        safe["configured"] = self.has_credentials()
        safe["shops_count"] = len(self.load_shops())
        _atomic_write(
            self.state_path,
            json.dumps(safe, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def load_shops(self) -> list[dict]:
        if not self.shops_path.is_file():
            return []
        try:
            value = json.loads(self.shops_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        return value if isinstance(value, list) else []

    def save_shops(self, shops: list[dict]) -> None:
        _atomic_write(
            self.shops_path,
            json.dumps(shops, ensure_ascii=False, indent=2).encode("utf-8"),
        )
