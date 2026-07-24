# -*- coding: utf-8 -*-
"""Synthetic endpoint and page tests for the local Lingxing integration."""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent import lingxing_integration
from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, TestOnlyProtector
from agent.settings import AgentSettings


class FakeProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        assert credentials.app_id == "synthetic-app-id"
        assert credentials.app_secret == "synthetic-app-secret"
        return [{
            "seller_id": "SELLER-TEST-1",
            "seller_name": "Synthetic Store",
            "country": "US",
            "status": "active",
            "ads_authorized": True,
        }]


def build_app(root: Path):
    return create_integrated_app(
        AgentSettings(data_root=root, allowed_origins=()),
        token="synthetic-test-token",
        provider_factory=FakeProvider,
        protector=TestOnlyProtector(),
        start_service=False,
    )


def test_lingxing_static_file_exists() -> None:
    path = Path(lingxing_integration.__file__).resolve().parent / "static" / "lingxing.html"
    assert path.is_file()
    assert "领星自动同步" in path.read_text(encoding="utf-8")


def test_lingxing_route_returns_success(tmp_path: Path) -> None:
    with TestClient(
        build_app(tmp_path),
        base_url="http://127.0.0.1:8766",
        raise_server_exceptions=False,
    ) as client:
        page = client.get("/lingxing")
        assert page.status_code == 200


def test_lingxing_page_contains_title(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        page = client.get("/lingxing")
        assert "领星自动同步" in page.text


def test_lingxing_page_has_no_external_resources(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        page = client.get("/lingxing")
        assert not re.search(r'<(?:script|img)[^>]+src=["\']https?://', page.text, re.I)
        assert not re.search(r'<(?:link|a)[^>]+href=["\']https?://', page.text, re.I)


def test_lingxing_page_does_not_embed_credentials(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        page = client.get("/lingxing")
        assert "synthetic-app-secret" not in page.text
        assert "type=\"password\"" in page.text


def test_lingxing_page_has_security_headers(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        page = client.get("/lingxing")
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["cache-control"] == "no-store"


def test_configure_status_sync_and_disconnect_never_return_secrets(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        client.get("/lingxing")
        payload = {
            "app_id": "synthetic-app-id",
            "app_secret": "synthetic-app-secret",
            "proxy_url": "http://127.0.0.1:18080",
            "auto_sync": False,
            "sync_interval_minutes": 120,
        }
        configured = client.post(
            "/v1/lingxing/configure",
            json=payload,
            headers={"Referer": "http://127.0.0.1:8766/lingxing"},
        )
        assert configured.status_code == 200
        text = configured.text
        assert "synthetic-app-secret" not in text
        assert "18080" not in text
        assert configured.json()["shops_count"] == 1

        status = client.get(
            "/v1/lingxing/status",
            headers={"Referer": "http://127.0.0.1:8766/lingxing"},
        )
        assert status.status_code == 200
        assert status.json()["shops"][0]["seller_name"] == "Synthetic Store"
        assert "app_secret" not in status.text
        assert "proxy_url" not in status.text

        synced = client.post(
            "/v1/lingxing/sync",
            headers={"Referer": "http://127.0.0.1:8766/lingxing"},
        )
        assert synced.status_code == 202

        deadline = time.monotonic() + 3
        while app.state.lingxing_service.is_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert app.state.lingxing_service.is_running() is False

        disconnected = client.delete(
            "/v1/lingxing/config",
            headers={"Referer": "http://127.0.0.1:8766/lingxing"},
        )
        assert disconnected.status_code == 200
        assert disconnected.json()["configured"] is False
        assert disconnected.json()["shops_count"] == 1


def test_cross_site_request_cannot_configure_lingxing(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path), base_url="http://127.0.0.1:8766") as client:
        client.get("/lingxing")
        response = client.post(
            "/v1/lingxing/configure",
            headers={"Origin": "https://malicious.example.test"},
            json={
                "app_id": "synthetic-app-id",
                "app_secret": "synthetic-app-secret",
                "proxy_url": "http://127.0.0.1:18080",
                "auto_sync": False,
                "sync_interval_minutes": 120,
            },
        )
        assert response.status_code == 401
