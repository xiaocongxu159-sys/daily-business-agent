# -*- coding: utf-8 -*-
"""Synthetic HTTP tests for the loopback-only Agent API."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agent.app import create_app
from agent.run_agent import build_parser
from agent.settings import AgentSettings

TOKEN = "synthetic-test-token"


def settings(root: Path, **overrides) -> AgentSettings:
    values = {
        "data_root": root,
        "host": "127.0.0.1",
        "port": 8766,
        "max_file_bytes": 10,
        "max_job_bytes": 100,
        "allowed_origins": (),
    }
    values.update(overrides)
    return AgentSettings(**values)


def test_launcher_has_no_token_printing_option() -> None:
    parser = build_parser()
    destinations = {action.dest for action in parser._actions}
    assert "show_token" not in destinations
    args = parser.parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8766


def test_health_is_open_but_job_api_requires_local_authorization(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), token=TOKEN)
    assert "src.excel_writer" not in sys.modules

    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/v1/jobs", headers={"X-Agent-Token": "wrong"}).status_code == 401
        assert client.get("/v1/jobs", headers={"X-Agent-Token": TOKEN}).status_code == 200


def test_local_page_issues_strict_session_and_security_headers(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), token=TOKEN)

    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "每日经营数据本地 Agent" in response.text
        assert "HttpOnly" in response.headers.get("set-cookie", "")
        assert "SameSite=strict" in response.headers.get("set-cookie", "")
        assert response.headers.get("x-frame-options") == "DENY"
        assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", "")
        assert "https://" not in response.text

        local = client.post(
            "/v1/jobs",
            headers={"Referer": "http://127.0.0.1:8766/"},
            json={"label": "Synthetic", "write_excel": False, "write_html": False},
        )
        assert local.status_code == 201

        foreign = client.post(
            "/v1/jobs",
            headers={"Origin": "https://malicious.example.test"},
            json={"label": "Blocked", "write_excel": False, "write_html": False},
        )
        assert foreign.status_code == 401


def test_upload_rejects_path_traversal_and_cleans_temp_files(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), token=TOKEN)

    with TestClient(app) as client:
        created = client.post(
            "/v1/jobs",
            headers={"X-Agent-Token": TOKEN},
            json={"label": "Synthetic", "write_excel": False, "write_html": False},
        )
        job_id = created.json()["job_id"]

        traversal = client.post(
            f"/v1/jobs/{job_id}/files/mapping",
            headers={"X-Agent-Token": TOKEN},
            files={"file": ("../escape.csv", b"x\n1\n", "text/csv")},
        )
        assert traversal.status_code == 400

        oversized = client.post(
            f"/v1/jobs/{job_id}/files/mapping",
            headers={"X-Agent-Token": TOKEN},
            files={"file": ("mapping.csv", b"01234567890", "text/csv")},
        )
        assert oversized.status_code == 400
        assert not list((tmp_path / "jobs").rglob("escape.csv"))
        assert not list((tmp_path / "jobs").rglob("*.uploading"))


def test_public_templates_use_only_synthetic_identifiers(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), token=TOKEN)

    with TestClient(app) as client:
        mapping = client.get(
            "/v1/templates/product-mapping.csv",
            headers={"X-Agent-Token": TOKEN},
        )
        assert mapping.status_code == 200
        assert "US-STORE-1" in mapping.text
        forbidden_shop_id = "92" + "26"
        assert forbidden_shop_id not in mapping.text

        plan = client.get(
            "/v1/templates/monthly-plan.xlsx",
            headers={"X-Agent-Token": TOKEN},
        )
        assert plan.status_code == 200
        assert plan.content.startswith(b"PK")
