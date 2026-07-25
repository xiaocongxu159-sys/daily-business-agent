# -*- coding: utf-8 -*-
"""Local-first Lingxing synchronization service."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Protocol

from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore


class LingxingProvider(Protocol):
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(?:tls\+http|https?|socks5h?)://[^\s/@:]+:[^\s/@]+@",
        "tls+http://***:***@",
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


class LingxingSyncService:
    def __init__(
        self,
        store: LingxingLocalStore,
        provider_factory: Callable[[], LingxingProvider],
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
            target=self._scheduler_loop, name="lingxing-auto-sync", daemon=True
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
            state.update(
                {
                    "status": "syncing",
                    "message": "正在通过固定出口获取最新店铺列表……",
                    "sync_reason": reason,
                    "sync_started_at": _utc_now(),
                }
            )
            self.store.save_state(state)
            try:
                shops = self.provider_factory().list_shops(credentials)
                if not isinstance(shops, list):
                    raise TypeError("领星店铺接口返回格式不正确")
                self.store.save_shops(shops)
                state.update(
                    {
                        "status": "success",
                        "message": f"店铺列表已更新，共 {len(shops)} 个店铺。",
                        "last_success_at": _utc_now(),
                        "last_error": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                message = self.friendly_error(exc, credentials)
                state.update(
                    {
                        "status": "failed",
                        "message": f"更新失败，继续显示本机上次成功数据。{message}",
                        "last_error": message,
                        "last_failure_at": _utc_now(),
                    }
                )
            finally:
                state["sync_finished_at"] = _utc_now()
                self.store.save_state(state)
            return self.public_status()

    @staticmethod
    def friendly_error(
        exc: Exception, credentials: LingxingCredentials | None = None
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
        if "fingerprint" in lowered or "指纹" in lowered:
            return "固定出口 TLS 证书校验失败。"
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
            proxy_url=credentials.proxy_url.strip(),
            auto_sync=credentials.auto_sync,
            sync_interval_minutes=int(credentials.sync_interval_minutes),
        ).validated()
        shops = self.provider_factory().list_shops(credentials)
        if not isinstance(shops, list):
            raise TypeError("领星店铺接口返回格式不正确")
        self.store.save_credentials(credentials)
        self.store.save_shops(shops)
        state = self.store.load_state()
        state.update(
            {
                "status": "success",
                "message": f"领星连接验证成功，共获取 {len(shops)} 个店铺。",
                "last_success_at": _utc_now(),
                "last_error": "",
                "transport": "pinned_outer_tls",
            }
        )
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
