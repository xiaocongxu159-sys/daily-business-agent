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
        "scripts/verify_running_upgrade.ps1",
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
        '"babel"',
    ):
        assert required in spec


def test_agent_runtime_declares_frozen_sdk_dependencies() -> None:
    build_requirements = read("requirements-build.txt")
    agent_requirements = read("requirements-agent.txt")
    assert "Babel==2.17.0" in build_requirements
    assert "Babel>=2.17,<3.0" in agent_requirements


def test_installer_preserves_accepted_upgrade_paths_and_single_file_association() -> None:
    installer = read("packaging/installer.iss")
    assert "PrivilegesRequired=lowest" in installer
    assert r"{localappdata}\CTJFyrdian\DailyBusinessAgentApp" in installer
    assert r"%LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent" in installer
    assert 'Software\Classes\.dba' in installer
    assert "导入每日经营连接包" in installer
    assert 'Type: filesandordirs; Name: "{app}\_internal"' in installer
    assert "[UninstallDelete]" not in installer


def test_installer_live_upgrade_is_fail_safe_before_runtime_delete() -> None:
    installer = read("packaging/installer.iss")
    required = (
        "AllowCancelDuringInstall=no",
        "CloseApplications=force",
        "CloseApplicationsFilter=*.*",
        "function PrepareToInstall",
        "taskkill /IM {#MyAppExeName} /T /F",
        "GetActiveTcpListeners()",
        "base_library.zip",
        "[IO.FileShare]::None",
        ".upgrade-backup",
        "PrepareRuntimeBackup",
        "RestoreRuntimeBackup",
        "procedure DeinitializeSetup",
        "RuntimeBackupPrepared and (not InstallSucceeded)",
    )
    for value in required:
        assert value in installer, value

    prepare_position = installer.index("function PrepareToInstall")
    backup_position = installer.index("PrepareRuntimeBackup", prepare_position)
    assert installer.index("StopAgentAndReleaseRuntime", prepare_position) < backup_position


def test_windows_gate_covers_running_upgrade_and_locked_runtime_abort() -> None:
    workflow = read(".github/workflows/windows-installer.yml")
    verifier = read("scripts/verify_running_upgrade.ps1")
    assert ".\\scripts\\verify_running_upgrade.ps1" in workflow
    for required in (
        "installer did not stop the running Agent process",
        "stale-running-upgrade-marker.txt",
        "local UI file is missing",
        "[IO.FileShare]::None",
        "locked-runtime upgrade returned unexpected exit code",
        "blocked upgrade modified existing",
        "preserve-running-upgrade.txt",
    ):
        assert required in verifier, required


def test_public_packaging_contains_no_private_server_material() -> None:
    # The scanner itself intentionally contains the forbidden regex patterns, so
    # only distributable/build files are checked here. The full repository is
    # independently checked by scripts/public_boundary_scan.py.
    combined = "\n".join(
        read(relative)
        for relative in (
            "packaging/agent_launcher.py",
            "packaging/DailyBusinessAgent.spec",
            "packaging/build_installer.ps1",
            "packaging/installer.iss",
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
