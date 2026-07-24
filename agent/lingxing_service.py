# -*- coding: utf-8 -*-
"""Local-only Lingxing shop synchronization with stale-while-revalidate behavior."""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Protocol
from urllib.parse import urlsplit

from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore

LOGGER = logging.getLogger(__name__)


class LingxingProvider(Protocol):
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_proxy_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "socks5", "socks5h"}:
        raise ValueError("固定出口代理只支持 http、socks5 或 socks5h")
    if not parsed.hostname or not parsed.port:
        raise ValueError("固定出口代理地址必须包含主机和端口")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("固定出口代理地址不能包含路径、查询参数或片段")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and (
        not parsed.username or not parsed.password
    ):
        raise ValueError("远程固定出口代理必须配置用户名和密码")
    return value.strip()


def _redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove credentials, tokens and authenticated proxy URLs from errors."""
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(https?|socks5h?)://[^\s/@:]+:[^\s/@]+@",
        r"\1://***:***@",
        text,
    )
    text = re.sub(
        r"(?i)\b(app[_ -]?secret|access[_ -]?token|refresh[_ -]?token|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=***",
        text,
    )
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text[:300]


class SdkLingxingProvider:
    """Adapter around the maintained third-party Lingxing Python SDK.

    Token handling and request signing happen on the Windows computer. HTTPS
    requests are sent through an authenticated CONNECT/SOCKS proxy; the relay
    forwards TLS bytes and does not terminate Lingxing HTTPS.
    """

    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        return asyncio.run(self._list_shops(credentials))

    async def _list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        try:
            from aiohttp_socks import ProxyConnector
            from lingxingapi import API
        except ImportError as exc:  # pragma: no cover - packaging CI catches this
            raise RuntimeError("领星 SDK 未正确安装") from exc

        proxy_url = validate_proxy_url(credentials.proxy_url)
        connector = ProxyConnector.from_url(proxy_url)
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
            shops.append({
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
            })
        return shops


class LingxingSyncService:
    """Keep the last successful local result visible while refreshing."""

    def __init__(
        self,
        store: LingxingLocalStore,
        provider_factory: Callable[[], LingxingProvider] = SdkLingxingProvider,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.provider_factory = provider_factory
        self.clock = clock
        self._sync_lock = threading.Lock()
        self._trigger_lock = threading.Lock()
        self._running = False
        self._pending = False
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._scheduler and self._scheduler.is_alive():
            return
        self._stop.clear()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            name="lingxing-auto-sync",
            daemon=True,
        )
        self._scheduler.start()
        if self.store.has_credentials():
            self.trigger("agent_start")

    def stop(self) -> None:
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(timeout=2)
        if self._worker:
            self._worker.join(timeout=5)

    def trigger(self, reason: str = "manual") -> bool:
        if not self.store.has_credentials():
            return False
        with self._trigger_lock:
            if self._running:
                self._pending = True
                return False
            self._running = True
            self._worker = threading.Thread(
                target=self._run_sync,
                args=(reason,),
                name="lingxing-shop-sync",
                daemon=True,
            )
            self._worker.start()
            return True

    def sync_now(self, reason: str = "manual") -> dict:
        return self._perform_sync(reason)

    def is_running(self) -> bool:
        with self._trigger_lock:
            return self._running

    def _run_sync(self, reason: str) -> None:
        try:
            self._perform_sync(reason)
        finally:
            rerun = False
            with self._trigger_lock:
                self._running = False
                if self._pending and not self._stop.is_set():
                    self._pending = False
                    rerun = True
            if rerun:
                self.trigger("pending")

    def _perform_sync(self, reason: str) -> dict:
        with self._sync_lock:
            credentials = self.store.load_credentials()
            state = self.store.load_state()
            state.update({
                "status": "syncing",
                "message": "正在通过固定出口获取最新店铺列表……",
                "sync_reason": reason,
                "sync_started_at": _utc_now(),
            })
            self.store.save_state(state)

            try:
                shops = self.provider_factory().list_shops(credentials)
                if not isinstance(shops, list):
                    raise TypeError("领星店铺接口返回格式不正确")
                self.store.save_shops(shops)
                state.update({
                    "status": "success",
                    "message": f"店铺列表已更新，共 {len(shops)} 个店铺。",
                    "last_success_at": _utc_now(),
                    "last_error": "",
                    "shops_count": len(shops),
                })
            except Exception as exc:  # noqa: BLE001 - boundary sanitizes persistence
                message = self.friendly_error(exc, credentials)
                state.update({
                    "status": "failed",
                    "message": f"更新失败，继续显示本机上次成功数据。{message}",
                    "last_error": message,
                    "last_failure_at": _utc_now(),
                })
            finally:
                state["sync_finished_at"] = _utc_now()
                self.store.save_state(state)
            return self.public_status()

    @staticmethod
    def friendly_error(
        exc: Exception,
        credentials: LingxingCredentials | None = None,
    ) -> str:
        secrets = ()
        if credentials is not None:
            secrets = (credentials.app_id, credentials.app_secret, credentials.proxy_url)
        name = type(exc).__name__
        text = _redact_text(str(exc), secrets)
        lowered = f"{name} {text}".lower()
        if "403" in lowered or "whitelist" in lowered or "white list" in lowered:
            return "请确认固定出口公网 IP 已加入领星白名单。"
        if "appid" in lowered or "app id" in lowered or "credential" in lowered or "unauthorized" in lowered:
            return "AppID 或 AppSecret 验证失败。"
        if "timeout" in lowered:
            return "连接领星超时，稍后会自动重试。"
        if "limit" in lowered or "429" in lowered:
            return "领星接口请求较多，稍后会自动重试。"
        return f"{name}: {text}"

    def public_status(self) -> dict:
        state = self.store.load_state()
        state["running"] = self.is_running()
        state["shops"] = self.store.load_shops()
        state["storage"] = "local_only"
        state["server_business_storage"] = False
        return state

    def save_and_test(self, credentials: LingxingCredentials) -> dict:
        credentials = LingxingCredentials(
            app_id=credentials.app_id.strip(),
            app_secret=credentials.app_secret.strip(),
            proxy_url=validate_proxy_url(credentials.proxy_url),
            auto_sync=credentials.auto_sync,
            sync_interval_minutes=int(credentials.sync_interval_minutes),
        ).validated()
        shops = self.provider_factory().list_shops(credentials)
        if not isinstance(shops, list):
            raise TypeError("领星店铺接口返回格式不正确")
        self.store.save_credentials(credentials)
        self.store.save_shops(shops)
        state = self.store.load_state()
        state.update({
            "status": "success",
            "message": f"领星连接验证成功，共获取 {len(shops)} 个店铺。",
            "last_success_at": _utc_now(),
            "last_error": "",
            "shops_count": len(shops),
        })
        self.store.save_state(state)
        return self.public_status()

    def disconnect(self) -> dict:
        self.store.disconnect()
        return self.public_status()

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(30):
            if not self.store.has_credentials() or self.is_running():
                continue
            try:
                credentials = self.store.load_credentials()
            except Exception:
                continue
            if not credentials.auto_sync:
                continue
            state = self.store.load_state()
            last_success = state.get("last_success_at")
            due = True
            if last_success:
                try:
                    previous = datetime.fromisoformat(last_success).timestamp()
                    due = self.clock() - previous >= credentials.sync_interval_minutes * 60
                except (TypeError, ValueError):
                    due = True
            if due:
                self.trigger("schedule")
