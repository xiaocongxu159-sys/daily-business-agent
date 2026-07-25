# -*- coding: utf-8 -*-
"""Pinned outer-TLS bridge for the Lingxing fixed-egress relay."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import socket
import ssl
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlsplit

from agent.lingxing_secure_store import LingxingCredentials
from agent.lingxing_service import LingxingProvider, LingxingSyncService

_LOCAL_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class TlsProxyTarget:
    host: str
    port: int
    username: str
    password: str
    certificate_sha256: str
    server_name: str


def _normalized_fingerprint(value: str) -> str:
    fingerprint = value.replace(":", "").strip().lower()
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise ValueError("固定出口 TLS 证书指纹必须是 64 位 SHA256 十六进制字符串")
    return fingerprint


def parse_tls_proxy_url(value: str) -> TlsProxyTarget:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "tls+http":
        raise ValueError("远程固定出口必须使用 tls+http 加密地址")
    if not parsed.hostname or not parsed.port:
        raise ValueError("固定出口 TLS 地址必须包含主机和端口")
    if not parsed.username or not parsed.password:
        raise ValueError("固定出口 TLS 地址必须包含用户名和密码")
    if parsed.path not in {"", "/"} or parsed.fragment:
        raise ValueError("固定出口 TLS 地址不能包含路径或片段")
    query = parse_qs(parsed.query, keep_blank_values=True)
    unknown = set(query) - {"sha256", "server_name"}
    if unknown:
        raise ValueError("固定出口 TLS 地址包含不支持的参数")
    fingerprint_values = query.get("sha256", [])
    if len(fingerprint_values) != 1:
        raise ValueError("固定出口 TLS 地址必须包含唯一的 sha256 证书指纹")
    server_name_values = query.get("server_name", [])
    if len(server_name_values) > 1:
        raise ValueError("固定出口 TLS 地址只能包含一个 server_name")
    server_name = server_name_values[0].strip() if server_name_values else parsed.hostname
    if not server_name:
        raise ValueError("固定出口 TLS server_name 不能为空")
    return TlsProxyTarget(
        host=parsed.hostname,
        port=int(parsed.port),
        username=unquote(parsed.username),
        password=unquote(parsed.password),
        certificate_sha256=_normalized_fingerprint(fingerprint_values[0]),
        server_name=server_name,
    )


def validate_secure_proxy_url(value: str) -> str:
    stripped = value.strip()
    parsed = urlsplit(stripped)
    if parsed.hostname in _LOCAL_PROXY_HOSTS:
        if parsed.scheme not in {"http", "socks5", "socks5h"}:
            raise ValueError("本机测试代理只支持 http、socks5 或 socks5h")
        if not parsed.port:
            raise ValueError("本机测试代理地址必须包含端口")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("本机测试代理地址不能包含路径、查询参数或片段")
        return stripped
    parse_tls_proxy_url(stripped)
    return stripped


class TlsProxyBridge:
    def __init__(self, proxy_url: str):
        self.target = parse_tls_proxy_url(proxy_url)
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._active_sockets: set[socket.socket] = set()
        self._lock = threading.Lock()
        self.last_error = ""

    @property
    def local_proxy_url(self) -> str:
        if self._listener is None:
            raise RuntimeError("TLS proxy bridge has not started")
        port = int(self._listener.getsockname()[1])
        return (
            f"http://{quote(self.target.username, safe='')}:{quote(self.target.password, safe='')}"
            f"@127.0.0.1:{port}"
        )

    def start(self) -> "TlsProxyBridge":
        if self._listener is not None:
            return self
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(0.5)
        self._listener = listener
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="lingxing-tls-proxy-accept", daemon=True
        )
        self._accept_thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            sockets = list(self._active_sockets)
        for item in sockets:
            try:
                item.close()
            except OSError:
                pass
        if self._accept_thread:
            self._accept_thread.join(timeout=2)
        for worker in list(self._workers):
            worker.join(timeout=2)

    def __enter__(self) -> "TlsProxyBridge":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            worker = threading.Thread(
                target=self._handle_client,
                args=(client,),
                name="lingxing-tls-proxy-connection",
                daemon=True,
            )
            self._workers.append(worker)
            worker.start()

    def _handle_client(self, client: socket.socket) -> None:
        remote: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        done = threading.Event()
        try:
            remote = socket.create_connection((self.target.host, self.target.port), timeout=15)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            tls_socket = context.wrap_socket(remote, server_hostname=self.target.server_name)
            certificate = tls_socket.getpeercert(binary_form=True)
            actual = hashlib.sha256(certificate or b"").hexdigest()
            if not hmac.compare_digest(actual, self.target.certificate_sha256):
                raise ssl.SSLError("固定出口 TLS 证书指纹不匹配")
            client.settimeout(1.0)
            tls_socket.settimeout(1.0)
            with self._lock:
                self._active_sockets.update({client, tls_socket})
            upstream = threading.Thread(
                target=self._pump,
                args=(client, tls_socket, done),
                name="lingxing-tls-proxy-upstream",
                daemon=True,
            )
            upstream.start()
            self._pump(tls_socket, client, done)
            upstream.join(timeout=2)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            done.set()
            with self._lock:
                self._active_sockets.discard(client)
                if tls_socket is not None:
                    self._active_sockets.discard(tls_socket)
            for item in (client, tls_socket, remote):
                if item is not None:
                    try:
                        item.close()
                    except OSError:
                        pass

    def _pump(self, source: socket.socket, destination: socket.socket, done: threading.Event) -> None:
        try:
            while not self._stop.is_set() and not done.is_set():
                try:
                    chunk = source.recv(64 * 1024)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                destination.sendall(chunk)
        except (OSError, ssl.SSLError):
            pass
        finally:
            done.set()
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass


@contextmanager
def secure_proxy_endpoint(proxy_url: str):
    validated = validate_secure_proxy_url(proxy_url)
    if urlsplit(validated).scheme != "tls+http":
        yield validated
        return
    with TlsProxyBridge(validated) as bridge:
        yield bridge.local_proxy_url


class TlsSdkLingxingProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        return asyncio.run(self._list_shops(credentials))

    async def _list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        try:
            from aiohttp_socks import ProxyConnector
            from lingxingapi import API
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("领星 SDK 未正确安装") from exc

        with secure_proxy_endpoint(credentials.proxy_url) as local_proxy_url:
            connector = ProxyConnector.from_url(local_proxy_url)
            async with API(
                credentials.app_id,
                credentials.app_secret,
                timeout=30,
                ignore_timeout=True,
                ignore_timeout_wait=2,
                ignore_timeout_retry=2,
                ignore_api_limit=True,
                ignore_api_limit_wait=2,
                ignore_api_limit_retry=3,
                proxy_connector=connector,
            ) as api:
                response = await api.basic.Sellers()

        shops = []
        for seller in getattr(response, "data", []) or []:
            if hasattr(seller, "model_dump"):
                values = seller.model_dump()
            elif hasattr(seller, "dict"):
                values = seller.dict()
            else:
                values = {
                    key: getattr(seller, key, None)
                    for key in (
                        "mid", "sid", "seller_id", "seller_name", "account_id",
                        "account_name", "marketplace_id", "region", "country",
                        "status", "ads_authorized",
                    )
                }
            shops.append(
                {
                    "mid": values.get("mid"),
                    "sid": values.get("sid"),
                    "seller_id": str(values.get("seller_id") or ""),
                    "seller_name": str(values.get("seller_name") or values.get("name") or ""),
                    "account_id": values.get("account_id") or values.get("seller_account_id"),
                    "account_name": str(values.get("account_name") or ""),
                    "marketplace_id": str(values.get("marketplace_id") or ""),
                    "region": str(values.get("region") or ""),
                    "country": str(values.get("country") or ""),
                    "status": values.get("status"),
                    "ads_authorized": bool(values.get("ads_authorized") or values.get("has_ads_setting")),
                }
            )
        return shops


class TlsLingxingSyncService(LingxingSyncService):
    def __init__(self, store, provider_factory=TlsSdkLingxingProvider, **kwargs):
        super().__init__(store, provider_factory=provider_factory, **kwargs)

    def save_and_test(self, credentials: LingxingCredentials) -> dict:
        credentials = LingxingCredentials(
            app_id=credentials.app_id.strip(),
            app_secret=credentials.app_secret.strip(),
            proxy_url=validate_secure_proxy_url(credentials.proxy_url),
            auto_sync=credentials.auto_sync,
            sync_interval_minutes=int(credentials.sync_interval_minutes),
        ).validated()
        return super().save_and_test(credentials)
