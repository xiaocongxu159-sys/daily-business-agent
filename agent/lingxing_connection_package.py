# -*- coding: utf-8 -*-
"""Strict parser for private Daily Business Agent connection packages."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from urllib.parse import quote

BUNDLE_SCHEMA = "daily-business-agent.connection-bundle.v1"
PAYLOAD_SCHEMA = "daily-business-agent.relay-connection.v1"
PACKAGE_SUFFIX = ".dba"
LEGACY_PACKAGE_SUFFIX = ".dba-connection.json"
MAX_PACKAGE_BYTES = 64 * 1024
MAX_CHECKSUM_BYTES = 1024

_DNS_NAME = re.compile(
    r"(?=^.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{8,128}$")
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ConnectionPackageError(ValueError):
    """Sanitized validation error safe to show in the loopback UI."""


@dataclass(frozen=True)
class RelayConnectionPackage:
    package_id: str
    created_at: str
    host: str
    port: int
    server_name: str
    username: str
    password: str = field(repr=False)
    certificate_sha256: str = field(repr=False)

    def proxy_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return (
            f"tls+http://{quote(self.username, safe='')}:{quote(self.password, safe='')}"
            f"@{host}:{self.port}?sha256={self.certificate_sha256}"
            f"&server_name={quote(self.server_name, safe='')}"
        )


def _leaf_name(value: str, label: str) -> str:
    name = value.strip()
    if not name or len(name) > 255:
        raise ConnectionPackageError(f"{label}文件名不正确")
    if PurePath(name).name != name or "/" in name or "\\" in name:
        raise ConnectionPackageError(f"{label}文件名不能包含目录")
    return name


def _valid_host(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ConnectionPackageError(f"{label}格式不正确")
    host = value.strip().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return host.lower()
    except ValueError:
        pass
    if not _DNS_NAME.fullmatch(host):
        raise ConnectionPackageError(f"{label}格式不正确")
    return host.lower()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConnectionPackageError("连接包包含重复字段")
        result[key] = value
    return result


def _require_exact_fields(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ConnectionPackageError(f"{label}字段不完整或包含未知字段")
    return value


def _parse_created_at(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ConnectionPackageError("连接包生成时间格式不正确")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectionPackageError("连接包生成时间格式不正确") from exc
    if parsed.tzinfo is None:
        raise ConnectionPackageError("连接包生成时间必须包含时区")
    return value


def _canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_payload(payload: object) -> RelayConnectionPackage:
    package = _require_exact_fields(
        payload,
        {"schema", "package_id", "created_at", "transport", "relay", "contains"},
        "连接包",
    )
    if package["schema"] != PAYLOAD_SCHEMA:
        raise ConnectionPackageError("连接包版本不受支持")
    if package["transport"] != "pinned_outer_tls":
        raise ConnectionPackageError("连接包传输方式不受支持")

    package_id = package["package_id"]
    if not isinstance(package_id, str) or not _HEX_32.fullmatch(package_id):
        raise ConnectionPackageError("连接包 ID 格式不正确")
    created_at = _parse_created_at(package["created_at"])

    relay = _require_exact_fields(
        package["relay"],
        {"scheme", "host", "port", "server_name", "username", "password", "certificate_sha256"},
        "中继配置",
    )
    if relay["scheme"] != "tls+http":
        raise ConnectionPackageError("连接包中继协议不受支持")
    host = _valid_host(relay["host"], "中继主机")
    server_name = _valid_host(relay["server_name"], "TLS server_name")

    port = relay["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConnectionPackageError("连接包中继端口不正确")

    username = relay["username"]
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise ConnectionPackageError("连接包代理用户名格式不正确")
    password = relay["password"]
    if (
        not isinstance(password, str)
        or not 32 <= len(password) <= 256
        or any(ord(char) < 33 or ord(char) > 126 for char in password)
    ):
        raise ConnectionPackageError("连接包代理密码格式不正确")

    fingerprint = relay["certificate_sha256"]
    if not isinstance(fingerprint, str):
        raise ConnectionPackageError("连接包证书指纹格式不正确")
    fingerprint = fingerprint.replace(":", "").strip().lower()
    if not _HEX_64.fullmatch(fingerprint):
        raise ConnectionPackageError("连接包证书指纹格式不正确")

    contains = _require_exact_fields(
        package["contains"],
        {"lingxing_app_id", "lingxing_app_secret", "tls_private_key", "business_data"},
        "连接包内容声明",
    )
    if any(value is not False for value in contains.values()):
        raise ConnectionPackageError("连接包包含不允许的敏感内容声明")

    return RelayConnectionPackage(
        package_id=package_id,
        created_at=created_at,
        host=host,
        port=port,
        server_name=server_name,
        username=username,
        password=password,
        certificate_sha256=fingerprint,
    )


def parse_connection_package(
    *,
    package_name: str,
    package_text: str,
) -> RelayConnectionPackage:
    package_name = _leaf_name(package_name, "连接包")
    if not package_name.lower().endswith(PACKAGE_SUFFIX):
        raise ConnectionPackageError(f"连接包文件名必须以 {PACKAGE_SUFFIX} 结尾")
    try:
        package_bytes = package_text.encode("utf-8")
    except UnicodeError as exc:
        raise ConnectionPackageError("连接包编码不正确") from exc
    if not package_bytes or len(package_bytes) > MAX_PACKAGE_BYTES:
        raise ConnectionPackageError("连接包大小不正确")
    if package_text.startswith("\ufeff"):
        raise ConnectionPackageError("连接包不能包含 UTF-8 BOM")

    try:
        bundle = json.loads(package_text, object_pairs_hook=_unique_object)
    except ConnectionPackageError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConnectionPackageError("连接包格式不正确") from exc

    bundle = _require_exact_fields(bundle, {"schema", "payload", "integrity"}, "连接包封装")
    if bundle["schema"] != BUNDLE_SCHEMA:
        raise ConnectionPackageError("连接包封装版本不受支持")

    integrity = _require_exact_fields(
        bundle["integrity"],
        {"algorithm", "payload_sha256"},
        "连接包完整性",
    )
    if integrity["algorithm"] != "sha256":
        raise ConnectionPackageError("连接包完整性算法不受支持")
    expected_digest = integrity["payload_sha256"]
    if not isinstance(expected_digest, str) or not _HEX_64.fullmatch(expected_digest.lower()):
        raise ConnectionPackageError("连接包完整性信息格式不正确")
    payload = bundle["payload"]
    if not isinstance(payload, dict):
        raise ConnectionPackageError("连接包内容格式不正确")
    actual_digest = hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()
    if not hmac.compare_digest(expected_digest.lower(), actual_digest):
        raise ConnectionPackageError("连接包完整性校验失败")
    return _parse_payload(payload)


def parse_connection_package_pair(
    *,
    package_name: str,
    package_text: str,
    checksum_name: str,
    checksum_text: str,
) -> RelayConnectionPackage:
    """Legacy parser retained only for controlled migration from 0.2.0."""
    package_name = _leaf_name(package_name, "连接包")
    checksum_name = _leaf_name(checksum_name, "校验")
    if not package_name.endswith(LEGACY_PACKAGE_SUFFIX):
        raise ConnectionPackageError(f"连接包文件名必须以 {LEGACY_PACKAGE_SUFFIX} 结尾")
    if checksum_name != package_name + ".sha256":
        raise ConnectionPackageError("SHA256 校验文件名与连接包不匹配")
    try:
        package_bytes = package_text.encode("utf-8")
        checksum_bytes = checksum_text.encode("ascii")
    except UnicodeError as exc:
        raise ConnectionPackageError("连接包或校验文件编码不正确") from exc
    if not package_bytes or len(package_bytes) > MAX_PACKAGE_BYTES:
        raise ConnectionPackageError("连接包大小不正确")
    if not checksum_bytes or len(checksum_bytes) > MAX_CHECKSUM_BYTES:
        raise ConnectionPackageError("SHA256 校验文件大小不正确")
    if package_text.startswith("\ufeff"):
        raise ConnectionPackageError("连接包不能包含 UTF-8 BOM")

    match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?(.+)", checksum_text.strip())
    if not match or match.group(2) != package_name:
        raise ConnectionPackageError("SHA256 校验文件内容不正确")
    expected_digest = match.group(1).lower()
    actual_digest = hashlib.sha256(package_bytes).hexdigest()
    if not hmac.compare_digest(expected_digest, actual_digest):
        raise ConnectionPackageError("连接包 SHA256 校验失败")
    try:
        payload = json.loads(package_text, object_pairs_hook=_unique_object)
    except ConnectionPackageError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConnectionPackageError("连接包 JSON 格式不正确") from exc
    return _parse_payload(payload)
