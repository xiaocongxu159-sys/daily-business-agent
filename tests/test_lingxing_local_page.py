# -*- coding: utf-8 -*-
"""Synthetic page and route tests for the unified Lingxing integration."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from agent import lingxing_integration
from agent.app import create_app
from agent.lingxing_integration import attach_lingxing, create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore, TestOnlyProtector
from agent.lingxing_tls_proxy import TlsLingxingSyncService
from agent.settings import AgentSettings
from agent.version import VERSION


class FakeProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        return [{
            "seller_id": "SELLER-TEST-1",
            "seller_name": "Synthetic Store",
            "country": "US",
            "status": "active",
            "ads_authorized": True,
        }]


def make_settings(root: Path) -> AgentSettings:
    return AgentSettings(data_root=root, allowed_origins=())


def build_app(root: Path):
    return create_integrated_app(
        make_settings(root),
        token="synthetic-test-token",
        provider_factory=FakeProvider,
        protector=TestOnlyProtector(),
        start_service=False,
    )


def lingxing_route(app):
    return next(route for route in app.routes if getattr(route, "path", "") == "/lingxing")


def test_lingxing_static_file_exists() -> None:
    path = Path(lingxing_integration.__file__).resolve().parent / "static" / "lingxing.html"
    assert path.is_file()
    html = path.read_text(encoding="utf-8")
    assert "领星自动同步" in html
    assert 'id="package-ready"' in html
    assert 'type="file"' not in html
    assert 'id="proxy-url"' not in html
    assert ".sha256" not in html


def test_base_agent_app_builds(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), token="synthetic-test-token")
    assert isinstance(app, FastAPI)
    assert hasattr(app.state, "sessions")


def test_lingxing_service_builds(tmp_path: Path) -> None:
    store = LingxingLocalStore(tmp_path, protector=TestOnlyProtector())
    service = TlsLingxingSyncService(store, provider_factory=FakeProvider)
    assert service.store is store
    assert service.is_running() is False


def test_attach_lingxing_returns_same_app(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), token="synthetic-test-token")
    attached = attach_lingxing(
        app,
        provider_factory=FakeProvider,
        protector=TestOnlyProtector(),
        start_service=False,
    )
    assert attached is app
    assert hasattr(app.state, "lingxing_store")
    assert hasattr(app.state, "pending_connection_packages")


def test_integrated_app_reports_candidate_version_and_both_pages(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == VERSION
        assert client.get("/").status_code == 200
        assert client.get("/lingxing").status_code == 200


def test_lingxing_route_endpoint_returns_html(tmp_path: Path) -> None:
    response = lingxing_route(build_app(tmp_path)).endpoint()
    assert isinstance(response, HTMLResponse)
    assert "领星自动同步" in response.body.decode("utf-8")


def test_lingxing_page_has_local_only_resources_and_security_headers(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        page = client.get("/lingxing")
        assert page.status_code == 200
        assert not re.search(r'<(?:script|img)[^>]+src=["\']https?://', page.text, re.I)
        assert not re.search(r'<(?:link|a)[^>]+href=["\']https?://', page.text, re.I)
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["cache-control"] == "no-store"
        assert "type=\"password\"" in page.text


def test_cross_site_request_cannot_stage_connection_package(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        client.get("/lingxing")
        response = client.post(
            "/v1/lingxing/stage-package",
            headers={"Origin": "https://malicious.example.test"},
            json={"source_path": str((tmp_path / "missing.dba").resolve())},
        )
        assert response.status_code == 401
