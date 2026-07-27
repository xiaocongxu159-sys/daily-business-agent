# -*- coding: utf-8 -*-
"""Native single-file .dba staging and safe source deletion."""
from __future__ import annotations

import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.lingxing_connection_package import (
    MAX_PACKAGE_BYTES,
    ConnectionPackageError,
    RelayConnectionPackage,
    parse_connection_package,
)


@dataclass(frozen=True)
class PendingConnectionPackage:
    token: str
    source_path: Path = field(repr=False)
    source_name: str
    connection: RelayConnectionPackage = field(repr=False)
    expires_at: float
    file_identity: tuple[int, int, int, int] = field(repr=False)


class PendingConnectionPackageStore:
    def __init__(self, lifetime_seconds: int = 15 * 60):
        self.lifetime_seconds = lifetime_seconds
        self._items: dict[str, PendingConnectionPackage] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _identity(details: os.stat_result) -> tuple[int, int, int, int]:
        return (
            int(details.st_dev),
            int(details.st_ino),
            int(details.st_size),
            int(details.st_mtime_ns),
        )

    @staticmethod
    def _resolve_local_file(path_text: str) -> tuple[Path, os.stat_result]:
        if not isinstance(path_text, str) or not path_text.strip() or len(path_text) > 4096:
            raise ConnectionPackageError("连接包路径不正确")
        raw = Path(path_text.strip()).expanduser()
        if not raw.is_absolute():
            raise ConnectionPackageError("连接包路径必须是本机绝对路径")
        if os.name == "nt" and str(raw).startswith(("\\\\", "//")):
            raise ConnectionPackageError("连接包必须位于本机磁盘，不能使用网络路径")
        try:
            details = os.lstat(raw)
        except OSError as exc:
            raise ConnectionPackageError("连接包文件不存在或无法读取") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise ConnectionPackageError("连接包必须是普通文件，不能是链接")
        if details.st_size < 1 or details.st_size > MAX_PACKAGE_BYTES:
            raise ConnectionPackageError("连接包大小不正确")
        try:
            resolved = raw.resolve(strict=True)
        except OSError as exc:
            raise ConnectionPackageError("连接包路径无法安全解析") from exc
        return resolved, details

    def _purge_expired(self, now: float) -> None:
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._items.pop(token, None)

    def stage_path(self, path_text: str) -> PendingConnectionPackage:
        source_path, details = self._resolve_local_file(path_text)
        if not source_path.name.lower().endswith(".dba"):
            raise ConnectionPackageError("连接包文件名必须以 .dba 结尾")
        try:
            package_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ConnectionPackageError("连接包无法读取或编码不正确") from exc
        connection = parse_connection_package(
            package_name=source_path.name,
            package_text=package_text,
        )
        token = secrets.token_urlsafe(24)
        now = time.time()
        item = PendingConnectionPackage(
            token=token,
            source_path=source_path,
            source_name=source_path.name,
            connection=connection,
            expires_at=now + self.lifetime_seconds,
            file_identity=self._identity(details),
        )
        with self._lock:
            self._purge_expired(now)
            self._items[token] = item
        return item

    def get(self, token: str) -> PendingConnectionPackage:
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            item = self._items.get(token)
        if item is None:
            raise ConnectionPackageError("连接包导入会话不存在或已过期，请重新双击 .dba 文件")
        return item

    def finish_and_delete(self, token: str) -> bool:
        item = self.get(token)
        try:
            details = os.lstat(item.source_path)
        except FileNotFoundError:
            deleted = True
        except OSError:
            deleted = False
        else:
            if self._identity(details) != item.file_identity:
                deleted = False
            elif stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                deleted = False
            else:
                deleted = False
                for _ in range(5):
                    try:
                        item.source_path.unlink()
                        deleted = True
                        break
                    except FileNotFoundError:
                        deleted = True
                        break
                    except PermissionError:
                        time.sleep(0.15)
                    except OSError:
                        break
        with self._lock:
            self._items.pop(token, None)
        return deleted

    def discard(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)
