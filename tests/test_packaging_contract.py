# -*- coding: utf-8 -*-
"""Static contract for unified public Windows packaging."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_required_public_files_exist() -> None:
    for relative in (
        "VERSION",
        "packaging/agent_launcher.py",
        "packaging/DailyBusinessAgent.spec",
        "packaging/build_installer.ps1",
        "packaging/installer.iss",
        "scripts/public_boundary_scan.py",
        "scripts/verify_windows_installer.ps1",
        "requirements-build.txt",
        "requirements-ci.txt",
        "README.md",
        "PRIVACY.md",
        "CHANGELOG.md",
        "THIRD_PARTY_LICENSES.md",
        "docs/WINDOWS_INSTALL.md",
        "docs/LINGXING.md",
        "docs/SECURITY_DESIGN.md",
        "docs/RELEASE_PROCESS.md",
    ):
        assert (ROOT / relative).is_file(), relative


def test_pyinstaller_bundles_both_pages_engine_config_and_sdk() -> None:
    spec = read("packaging/DailyBusinessAgent.spec")
    for required in (
        "VERSION",
        "index.html",
        "lingxing.html",
        "api_config.json",
        "field_aliases.json",
        "LICENSE",
        "NOTICE",
        '"lingxingapi"',
        '"aiohttp_socks"',
        '"Crypto"',
    ):
        assert required in spec


def test_installer_preserves_accepted_upgrade_paths_and_single_file_association() -> None:
    installer = read("packaging/installer.iss")
    assert "PrivilegesRequired=lowest" in installer
    assert r"{localappdata}\CTJFyrdian\DailyBusinessAgentApp" in installer
    assert r"%LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent" in installer
    assert 'Software\Classes\.dba' in installer
    assert "导入每日经营连接包" in installer
    assert 'Type: filesandordirs; Name: "{app}\_internal"' in installer
    assert "[UninstallDelete]" not in installer


def test_public_packaging_contains_no_private_server_material() -> None:
    combined = "\n".join(
        read(relative)
        for relative in (
            "packaging/agent_launcher.py",
            "packaging/DailyBusinessAgent.spec",
            "packaging/build_installer.ps1",
            "packaging/installer.iss",
            "scripts/public_boundary_scan.py",
        )
    )
    forbidden = (
        "124." + "221.26.163",
        "dba-egress." + "ctjfyrdian.com",
        "/home" + "/ubuntu",
        "amazon-keyword" + "-rank-monitor-dev",
        "BEGIN " + "PRIVATE KEY",
    )
    for value in forbidden:
        assert value not in combined


def test_build_script_is_dynamic_and_writes_sha256() -> None:
    script = read("packaging/build_installer.ps1")
    assert 'Get-Content -Raw "VERSION"' in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "pip check" in script
    assert "pytest -q" in script
    assert "public_boundary_scan.py" in script
