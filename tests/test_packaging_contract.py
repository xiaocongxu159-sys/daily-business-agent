# -*- coding: utf-8 -*-
"""Static safety contract for the public Windows packaging files."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_required_packaging_files_exist() -> None:
    for relative in (
        "packaging/agent_launcher.py",
        "packaging/DailyBusinessAgent.spec",
        "packaging/build_installer.ps1",
        "packaging/installer.iss",
        "packaging/version_info.txt",
        "requirements-ci.txt",
    ):
        assert (ROOT / relative).is_file(), relative


def test_pyinstaller_bundles_both_local_pages_and_public_notices() -> None:
    spec = read("packaging/DailyBusinessAgent.spec")
    for required in (
        "index.html",
        "lingxing.html",
        "api_config.json",
        "field_aliases.json",
        "LICENSE",
        "NOTICE",
    ):
        assert required in spec
    assert '"lingxingapi"' in spec
    assert '"aiohttp_socks"' in spec


def test_installer_uses_neutral_user_scope_and_preserves_data() -> None:
    installer = read("packaging/installer.iss")
    assert "PrivilegesRequired=lowest" in installer
    assert r"{localappdata}\Programs\DailyBusinessAgent" in installer
    assert r"{userstartup}" in installer
    assert r"%LOCALAPPDATA%\DailyBusinessAgent" in installer
    assert "删除用户原始文件" in installer
    assert "DelTree" not in installer
    assert "[UninstallDelete]" not in installer


def test_public_packaging_contains_no_internal_identifiers() -> None:
    combined = "\n".join(
        read(relative)
        for relative in (
            "packaging/agent_launcher.py",
            "packaging/DailyBusinessAgent.spec",
            "packaging/build_installer.ps1",
            "packaging/installer.iss",
            "packaging/version_info.txt",
        )
    )
    forbidden = (
        "CTJ" + "Fyrdian",
        "/home" + "/ubuntu",
        "amazon-keyword" + "-rank-monitor-dev",
        "feature/daily-business-agent" + "-installer",
        "integration/daily-business-report" + "-prod-baseline",
    )
    for value in forbidden:
        assert value not in combined


def test_build_script_writes_sha256_metadata() -> None:
    script = read("packaging/build_installer.ps1")
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "pip check" in script
    assert "pytest -q" in script
