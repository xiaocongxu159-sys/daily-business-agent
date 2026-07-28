# -*- coding: utf-8 -*-
"""Attach single-file Lingxing synchronization to the complete local Agent."""
from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, SecretStr

from agent.app import create_app
from agent.lingxing_connection_package import ConnectionPackageError
from agent.lingxing_package_import import PendingConnectionPackageStore
from agent.lingxing_probe import (
    LingxingProbeProvider,
    LingxingProbeResultStore,
    LingxingReadOnlyProbeService,
    TlsSdkLingxingProbeProvider,
)
from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore, SecretProtector
from agent.lingxing_service import LingxingProvider
from agent.lingxing_tls_proxy import TlsLingxingSyncService, TlsSdkLingxingProvider
from agent.settings import AgentSettings
from agent.version import VERSION


class LingxingStagePackageRequest(BaseModel):
    source_path: SecretStr = Field(min_length=1, max_length=4096)


class LingxingPackageConfigureRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: SecretStr
    import_token: SecretStr = Field(min_length=20, max_length=200)
    auto_sync: bool = True
    sync_interval_minutes: int = Field(default=120, ge=30, le=1440)


def _apply_local_page_headers(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def attach_lingxing(
    app: FastAPI,
    *,
    provider_factory: Callable[[], LingxingProvider] = TlsSdkLingxingProvider,
    probe_provider_factory: Callable[[], LingxingProbeProvider] = TlsSdkLingxingProbeProvider,
    protector: SecretProtector | None = None,
    start_service: bool = True,
) -> FastAPI:
    settings: AgentSettings = app.state.settings
    local_ui_path = Path(__file__).resolve().parent / "static" / "lingxing.html"
    store = LingxingLocalStore(settings.data_root, protector=protector)
    service = TlsLingxingSyncService(store, provider_factory=provider_factory)
    probe_store = LingxingProbeResultStore(settings.data_root)
    probe_service = LingxingReadOnlyProbeService(
        store,
        probe_store,
        provider_factory=probe_provider_factory,
    )
    pending_packages = PendingConnectionPackageStore()
    app.state.lingxing_store = store
    app.state.lingxing_service = service
    app.state.lingxing_probe_store = probe_store
    app.state.lingxing_probe_service = probe_service
    app.state.pending_connection_packages = pending_packages

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def integrated_lifespan(app_instance: FastAPI):
        async with original_lifespan(app_instance):
            try:
                yield
            finally:
                probe_service.stop()
                service.stop()

    app.router.lifespan_context = integrated_lifespan

    def is_local_ui_request(request: Request) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        referer = request.headers.get("referer", "")
        allowed = {
            f"http://127.0.0.1:{settings.port}",
            f"http://localhost:{settings.port}",
        }
        if origin in allowed or any(referer.startswith(value + "/") for value in allowed):
            return True
        return request.headers.get("sec-fetch-site", "") in {"same-origin", "none"}

    def require_local_auth(
        request: Request,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
        agent_session: str | None = Cookie(default=None),
    ) -> None:
        if x_agent_token and secrets.compare_digest(x_agent_token, app.state.agent_token):
            return
        if app.state.sessions.valid(agent_session) and is_local_ui_request(request):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid local agent authorization",
        )

    @app.get("/lingxing", response_class=HTMLResponse)
    def lingxing_ui() -> HTMLResponse:
        if not local_ui_path.is_file():
            raise HTTPException(status_code=500, detail="Lingxing local UI file is missing")
        response = _apply_local_page_headers(
            HTMLResponse(local_ui_path.read_text(encoding="utf-8"))
        )
        response.set_cookie(
            "agent_session",
            app.state.sessions.issue(),
            max_age=app.state.sessions.lifetime_seconds,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
        service.trigger("page_open")
        return response

    @app.get("/v1/lingxing/status", dependencies=[Depends(require_local_auth)])
    def lingxing_status() -> dict:
        return service.public_status()

    @app.get("/v1/lingxing/shops", dependencies=[Depends(require_local_auth)])
    def lingxing_shops() -> dict:
        return {"shops": store.load_shops(), "state": service.public_status()}

    @app.get("/v1/lingxing/probe", dependencies=[Depends(require_local_auth)])
    def lingxing_probe_status() -> dict:
        return probe_service.public_status()

    @app.post(
        "/v1/lingxing/probe",
        status_code=202,
        dependencies=[Depends(require_local_auth)],
    )
    def run_lingxing_probe() -> dict:
        if not store.has_credentials():
            raise HTTPException(status_code=400, detail="尚未配置领星连接")
        started = probe_service.trigger()
        return {
            "started": started,
            "message": "已开始只读字段探测。" if started else "只读字段探测已经在运行。",
            "state": probe_service.public_status(),
        }

    @app.post("/v1/lingxing/stage-package", dependencies=[Depends(require_local_auth)])
    def stage_lingxing_package(payload: LingxingStagePackageRequest) -> dict:
        try:
            item = pending_packages.stage_path(payload.source_path.get_secret_value())
        except ConnectionPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "import_token": item.token,
            "package_name": item.source_name,
            "expires_in_seconds": pending_packages.lifetime_seconds,
        }

    @app.get(
        "/v1/lingxing/pending-package/{import_token}",
        dependencies=[Depends(require_local_auth)],
    )
    def pending_lingxing_package(import_token: str) -> dict:
        try:
            item = pending_packages.get(import_token)
        except ConnectionPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "package_name": item.source_name,
            "expires_in_seconds": max(0, int(item.expires_at - time.time())),
        }

    @app.post("/v1/lingxing/configure-package", dependencies=[Depends(require_local_auth)])
    def configure_lingxing_package(payload: LingxingPackageConfigureRequest) -> dict:
        import_token = payload.import_token.get_secret_value()
        try:
            item = pending_packages.get(import_token)
        except ConnectionPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        credentials = LingxingCredentials(
            app_id=payload.app_id,
            app_secret=payload.app_secret.get_secret_value(),
            proxy_url=item.connection.proxy_url(),
            auto_sync=payload.auto_sync,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
        try:
            result = service.save_and_test(credentials)
        except Exception as exc:  # noqa: BLE001 - endpoint returns a sanitized message
            message = service.friendly_error(exc, credentials)
            raise HTTPException(status_code=400, detail=message) from exc

        source_deleted = pending_packages.finish_and_delete(import_token)
        result["connection_package_imported"] = True
        result["source_deleted"] = source_deleted
        return result

    @app.post("/v1/lingxing/sync", status_code=202, dependencies=[Depends(require_local_auth)])
    def sync_lingxing() -> dict:
        if not store.has_credentials():
            raise HTTPException(status_code=400, detail="尚未配置领星连接")
        started = service.trigger("manual")
        return {
            "started": started,
            "message": "已开始后台更新。" if started else "后台更新已经在运行。",
            "state": service.public_status(),
        }

    @app.delete("/v1/lingxing/config", dependencies=[Depends(require_local_auth)])
    def disconnect_lingxing() -> dict:
        return service.disconnect()

    if start_service:
        service.start()
    return app


def create_integrated_app(
    settings: AgentSettings | None = None,
    token: str | None = None,
    *,
    provider_factory: Callable[[], LingxingProvider] = TlsSdkLingxingProvider,
    probe_provider_factory: Callable[[], LingxingProbeProvider] = TlsSdkLingxingProbeProvider,
    protector: SecretProtector | None = None,
    start_service: bool = True,
) -> FastAPI:
    app = create_app(settings, token=token)
    app.version = VERSION
    return attach_lingxing(
        app,
        provider_factory=provider_factory,
        probe_provider_factory=probe_provider_factory,
        protector=protector,
        start_service=start_service,
    )
