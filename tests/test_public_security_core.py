# -*- coding: utf-8 -*-
"""Public-safe tests for local credential and transport boundaries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.lingxing_secure_store import (
    LingxingCredentials,
    LingxingLocalStore,
    TestOnlyProtector,
)
from agent.lingxing_service import _redact_text
from agent.lingxing_tls_proxy import parse_tls_proxy_url, validate_secure_proxy_url
from agent.settings import AgentSettings, default_data_root


def test_default_data_root_uses_neutral_product_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_data_root() == tmp_path / "DailyBusinessAgent"


def test_agent_rejects_non_loopback_bind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        AgentSettings(data_root=tmp_path, host="0.0.0.0").validated()


def test_lingxing_credentials_are_encrypted_and_state_is_sanitized(tmp_path: Path) -> None:
    store = LingxingLocalStore(tmp_path, protector=TestOnlyProtector())
    credentials = LingxingCredentials(
        app_id="synthetic-app-id",
        app_secret="synthetic-app-secret",
        proxy_url="http://test-user:test-pass@127.0.0.1:18080",
        auto_sync=True,
        sync_interval_minutes=120,
    )

    store.save_credentials(credentials)

    encrypted_text = store.credentials_path.read_text(encoding="utf-8")
    assert credentials.app_secret not in encrypted_text
    assert credentials.proxy_url not in encrypted_text
    assert store.load_credentials() == credentials

    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    for forbidden in ("app_id", "app_secret", "proxy_url", "access_token", "refresh_token"):
        assert forbidden not in state
    assert state["app_id_hint"].endswith("p-id")


def test_error_redaction_removes_secrets_and_proxy_authentication() -> None:
    raw = (
        "app_secret=synthetic-secret access_token=synthetic-token "
        "http://test-user:test-pass@relay.example.test:8080"
    )
    redacted = _redact_text(raw, ("synthetic-secret", "synthetic-token"))

    assert "synthetic-secret" not in redacted
    assert "synthetic-token" not in redacted
    assert "test-user" not in redacted
    assert "test-pass" not in redacted
    assert "***:***@" in redacted


def test_tls_proxy_configuration_uses_synthetic_runtime_values() -> None:
    fingerprint = "ab" * 32
    value = (
        "tls+http://test-user:test-pass@203.0.113.10:8443"
        f"?sha256={fingerprint}&server_name=relay.example.test"
    )

    target = parse_tls_proxy_url(value)

    assert target.host == "203.0.113.10"
    assert target.port == 8443
    assert target.username == "test-user"
    assert target.password == "test-pass"
    assert target.certificate_sha256 == fingerprint
    assert target.server_name == "relay.example.test"


def test_remote_tls_proxy_requires_a_sha256_fingerprint() -> None:
    with pytest.raises(ValueError, match="sha256"):
        validate_secure_proxy_url(
            "tls+http://test-user:test-pass@203.0.113.10:8443"
        )


def test_loopback_proxy_is_allowed_for_local_testing() -> None:
    assert validate_secure_proxy_url("http://127.0.0.1:18080") == "http://127.0.0.1:18080"
