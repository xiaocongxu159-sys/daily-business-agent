# -*- coding: utf-8 -*-
"""Attach the package-only Lingxing UI and endpoints to the loopback Agent."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Callable

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, SecretStr

from agent.app import create_app
from agent.lingxing_connection_package import ConnectionPackageError, parse_connection_package_pair
from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore, SecretProtector
from agent.lingxing_service import LingxingProvider
from agent.lingxing_tls_proxy import TlsLingxingSyncService, TlsSdkLingxingProvider
from agent.settings import AgentSettings


class LingxingPackageConfigureRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: SecretStr
    package_name: str = Field(min_length=1, max_length=255)
    package_text: SecretStr = Field(min_length=1, max_length=64 * 1024)
    checksum_name: str = Field(min_length=1, max_length=255)
    checksum_text: SecretStr = Field(min_length=1, max_length=1024)
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
    protector: SecretProtector | None = None,
    start_service: bool = True,
) -> FastAPI:
    settings: AgentSettings = app.state.settings
    local_ui_path = Path(__file__).resolve().parent / "static" / "lingxing.html"
    store = LingxingLocalStore(settings.data_root, protector=protector)
    service = TlsLingxingSyncService(store, provider_factory=provider_factory)
    app.state.lingxing_store = store
    app.state.lingxing_service = service
    app.add_event_handler("shutdown", service.stop)

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
        response = _apply_local_page_headers(HTMLResponse(local_ui_path.read_text(encoding="utf-8")))
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

    @app.post("/v1/lingxing/configure-package", dependencies=[Depends(require_local_auth)])
    def configure_lingxing_package(payload: LingxingPackageConfigureRequest) -> dict:
        try:
            connection = parse_connection_package_pair(
                package_name=payload.package_name,
                package_text=payload.package_text.get_secret_value(),
                checksum_name=payload.checksum_name,
                checksum_text=payload.checksum_text.get_secret_value(),
            )
        except ConnectionPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        credentials = LingxingCredentials(
            app_id=payload.app_id,
            app_secret=payload.app_secret.get_secret_value(),
            proxy_url=connection.proxy_url(),
            auto_sync=payload.auto_sync,
            sync_interval_minutes=payload.sync_interval_minutes,
        )
        try:
            result = service.save_and_test(credentials)
        except Exception as exc:  # noqa: BLE001
            message = service.friendly_error(exc, credentials)
            raise HTTPException(status_code=400, detail=message) from exc
        result["connection_package_imported"] = True
        result["relay"] = {"host": connection.host, "port": connection.port}
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
    protector: SecretProtector | None = None,
    start_service: bool = True,
) -> FastAPI:
    app = create_app(settings, token=token)
    return attach_lingxing(
        app,
        provider_factory=provider_factory,
        protector=protector,
        start_service=start_service,
    )
